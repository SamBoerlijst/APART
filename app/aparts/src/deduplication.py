import os
from itertools import combinations, cycle
from math import sqrt, ceil

import matplotlib
import matplotlib.cm as cm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import pairwise_distances
from sklearn.preprocessing import StandardScaler

from aparts.src.subsampling import (assign_group, generate_binary_item_matrix,
                                    transform_dataframe)

matplotlib.use('TkAgg')


def group_tags_by_dissimilarity(dissimilarity_matrix: np.ndarray, tag_names: list, threshold: float = 0.5, print_output: bool = False) -> list:
    """
    Group tags based on dissimilarity using Agglomerative Clustering.

    Parameters:
    -----------
    dissimilarity_matrix (np.ndarray): Matrix containing dissimilarity values between tags.

    tag_names (list): List of tag names corresponding to the dissimilarity matrix.

    threshold (float, optional): Distance threshold for clustering. Default is 0.5.

    print_output (bool, optional): Flag to print additional output. Default is False.

    Return:
    -----------
    nnamed_groups (list): List of groups, where each group is a list of tag names with low dissimilarity.
    """
    clustering = AgglomerativeClustering(
        n_clusters=None, linkage='average', distance_threshold=threshold, metric='precomputed')
    clusters = clustering.fit_predict(dissimilarity_matrix)

    grouped_columns = {}
    for col_idx, cluster_id in enumerate(clusters):
        if cluster_id not in grouped_columns:
            grouped_columns[cluster_id] = []
        grouped_columns[cluster_id].append(col_idx)

    # Filter clusters to include only those with 2 or more items
    filtered_groups = [
        group for group in grouped_columns.values() if len(group) >= 2]

    # Substitute indices with tag names
    nnamed_groups = [[tag_names[col_idx] for col_idx in group]
                     for group in filtered_groups]

    return nnamed_groups


def generate_tag_dissimilarity(dataframe: pd.DataFrame) -> np.ndarray:
    """
    Generate a dissimilarity matrix for tags based on binary presence/absence in a DataFrame.

    Parameters:
    -----------
    dataframe (pd.DataFrame): DataFrame containing binary information for tag presence/absence.

    Return:
    -----------
    np.ndarray: Dissimilarity matrix calculated using Bray-Curtis distance.
    """
    binary_dataframe = dataframe.astype(int)
    matrix = binary_dataframe.values
    dissimilarity_matrix = pairwise_distances(matrix.T, metric='braycurtis')
    return dissimilarity_matrix


def deduplicate_tag_conjugations(word_list: list, method: str = "", deleted_pairs: list = None) -> list:
    """
    Identify and delete duplicate tags based on their stem from a (nested) list.

    This function takes a nested list of words and performs deduplication by identifying duplicate tags based on their stem.
    It also provides an option to output the identified duplicates so that their data can be merged in their corresponding matrix.

    Parameters:
    -----------
    word_list (list): The nested list of words to be deduplicated.

    method (str): The deduplication method to use. Can be either "deduplicated" or "pairs".

    deleted_pair (list): A list to retain identified duplicate pairs across iterations. Used when method is set to "pairs" and the input is a nested list.

    Returns:
    --------
    list: A deduplicated version of the input nested list. If method is "deduplicated", 
          this list contains the deduplicated words and sublists. If method is "pairs", 
          this list contains pairs of duplicate words and their corresponding stems.
    """
    if deleted_pairs is None:
        deleted_pairs = []

    sorted_words = sorted(word_list, key=len, reverse=True)
    processed_prefixes = set()
    deduplicated = []

    for word in sorted_words:
        pair = ()
        if isinstance(word, list):
            deduplicated_sublist = deduplicate_tag_conjugations(
                word, method, deleted_pairs)
            deduplicated.append(deduplicated_sublist)
        elif isinstance(word, str):
            is_duplicate = any(word.startswith(
                existing_prefix[:len(word)]) for existing_prefix in processed_prefixes)
            if is_duplicate:
                pair = (word, next(existing_prefix for existing_prefix in processed_prefixes if word.startswith(
                    existing_prefix[:len(word)])))
                deleted_pairs.append(pair)
            else:
                deduplicated.append(word)
                processed_prefixes.add(word)

    return deleted_pairs if method == "pairs" else deduplicated


def deduplicate_dataframe(dataframe: pd.DataFrame, pairs_list: list, mode: str = "strict") -> pd.DataFrame:
    """
    Deduplicate a DataFrame based on the provided pairs and mode.

    Parameters:
    -----------
    dataframe (pd.DataFrame): The DataFrame to be deduplicated.

    pairs_list (list): A list of pairs, where each pair is a tuple containing source and sink column names.

    mode (str): The deduplication mode. Can be 'strict' or 'lenient'. In strict mode the contents of the source column are copied to the sink column. 
    The source column is subsequently deleted. In lenient mode, the source column is deleted if its contents are identical to the sink column.

    Returns:
    --------
    pd.DataFrame: The deduplicated DataFrame.
    """
    deduplicated_dataframe = dataframe.copy()

    for pair in pairs_list:
        source, sink = pair

        if source not in deduplicated_dataframe.columns or sink not in deduplicated_dataframe.columns:
            continue

        sourcedata = deduplicated_dataframe[source]
        sinkdata = deduplicated_dataframe[sink]

        if mode == "strict":
            for i in range(len(sourcedata)):
                if sourcedata[i] > 0:
                    sinkdata[i] = sourcedata[i]
            deduplicated_dataframe[sink] = sinkdata
            deduplicated_dataframe.drop(columns=[source], inplace=True)

        if mode == "lenient":
            if sourcedata.equals(sinkdata):
                deduplicated_dataframe.drop(columns=[source], inplace=True)

    return deduplicated_dataframe


def merge_similar_tags_from_dataframe(input_file: str, output: str, variables: str, id: str, tag_length: int,  number_of_records: int = "", threshold: float = 0.6, manual: bool = False, show_output: bool = False):
    """
    Merges similar tags in a DataFrame based on tag similarity using various deduplication methods.

    Parameters:
    -----------
    input_file (str): Path to the CSV file containing the data.

    output (str): Filename for an output csv. Data is not saved, but only returned, if left blank.

    variables (str): Comma-separated list of tag/column names.

    id (str): Column name containing unique titles/identifiers.

    tag_length (int): Length of tag n-grams for similarity comparison.

    number_of_records (int): Select only the top n records.

    threshold (float, optional): Similarity threshold for grouping tags. Default is 0.6.

    manual (bool): include manual check of potential duplicates by y/n prompt per pair to either merge or discard ('q' to escape). Default is False.

    show_output (bool): Whether to display intermediate output. Default is False.

    Returns:
    --------
    pd.DataFrame: DataFrame with similar tags merged using the specified deduplication methods.
    """
    def generate_tuple_combinations(nested_list: str) -> list:
        """
        Generate all possible combinations of tag pairs from a nested list of tags.
        """
        combination = []

        for item in nested_list:
            product = []
            product = list(combinations(item, 2))
            combination.extend(product)

        return combination

    def calculate_tag_similarity(Dataframe: pd.DataFrame, method: str, treshold: float) -> tuple[list, np.ndarray]:
        """
        Calculate tag similarity and deduplicate similar tags in the DataFrame.
        """
        tag_dissimilarity = generate_tag_dissimilarity(Dataframe)
        tag_names = Dataframe.columns
        grouped_tags = group_tags_by_dissimilarity(
            tag_dissimilarity, tag_names, treshold)
        deduplicated_tags = deduplicate_tag_conjugations(grouped_tags, method)

        return deduplicated_tags, tag_dissimilarity

    def manual_deduplication(tuples, dataframe):
        """
        Suggest potential duplicates and merge or discard based on user input"""
        deduplicated_dataframe = dataframe.copy()

        for source, sink in tuples:
            if source in deduplicated_dataframe.columns and sink in deduplicated_dataframe.columns:
                sourcedata = deduplicated_dataframe[source]
                sinkdata = deduplicated_dataframe[sink]
                answer = input(f"Merge {source} to {sink}? (y/n): ").lower()
                if answer == "y":
                    print(f"Merged {source} to {sink}")
                    sinkdata = sinkdata.add(sourcedata, fill_value=0)
                    deduplicated_dataframe[sink] = sinkdata
                    deduplicated_dataframe.drop(columns=[source], inplace=True)
                elif answer == "n":
                    break
                elif answer == "q":
                    return deduplicated_dataframe
                else:
                    print("Input invalid. Please answer with 'y' or 'n'")

        return deduplicated_dataframe

    matrix = generate_binary_item_matrix(
        input_file, variables, id, tag_length, number_of_records)[0]
    matrix = drop_0_columns(matrix)
    deduplicated_tags = calculate_tag_similarity(matrix, "pairs", threshold)[0]

    matrix_deduplicated = deduplicate_dataframe(
        matrix, deduplicated_tags, "strict")
    deduplicated_tags_control = calculate_tag_similarity(
        matrix_deduplicated, "", threshold)[0]
    potential_duplicates = generate_tuple_combinations(
        deduplicated_tags_control)

    matrix_deduplicated_lenient = deduplicate_dataframe(
        matrix, potential_duplicates, "lenient")
    deduplicated_tags_remaining = calculate_tag_similarity(
        matrix_deduplicated_lenient, "", threshold)[0]
    remaining_duplicates = [
        item for item in deduplicated_tags_remaining if len(item) > 1]
    remaining_duplicate_tuple = generate_tuple_combinations(
        remaining_duplicates)

    if manual:
        matrix_deduplicated_manual = manual_deduplication(
            remaining_duplicate_tuple, matrix_deduplicated_lenient)
    else:
        matrix_deduplicated_manual = matrix_deduplicated_lenient

    if show_output:
        deduplicated_tags_remaining, tag_dissimilarity3 = calculate_tag_similarity(
            matrix_deduplicated_manual, "", threshold)
        plt.figure()
        plt.imshow(tag_dissimilarity3)

    if output:
        matrix_deduplicated_manual.to_csv(output, sep=',')

    return matrix_deduplicated_manual


def count_tag_occurrence(dataframe: pd.DataFrame) -> np.array:
    """
    Count the occurrence of tags in a binary DataFrame.

    Parameters:
    -----------
    dataframe (pd.DataFrame): DataFrame containing binary information for tag presence/absence.

    Return:
    -----------
    np.array: Array of tuples containing tag names and their corresponding occurrence counts.
    """
    # Assuming the binary columns start from column index 1
    binary_columns = dataframe.columns[1:]

    counts = []
    for column in binary_columns:
        count = dataframe[column].sum()
        if count > 0:
            counts.append((column, count))
    counts.sort(key=lambda x: x[1], reverse=True)
    return counts


def drop_0_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    """
    Delete any columns without observations from the given dataframe.
    """
    zero_columns = dataframe.columns[(dataframe == 0).all()]
    dataframe.drop(zero_columns, axis=1, inplace=True)
    return dataframe


def drop_unique_columns(Dataframe: pd.DataFrame) -> pd.DataFrame:
    """
    Drop all single observation columns from a dataframe    
    """
    filtered_dataframe = Dataframe.copy()
    counts = count_tag_occurrence(Dataframe)
    for item, count in counts:
        if count == 1:
            filtered_dataframe.drop(columns=[item], inplace=True)
    return filtered_dataframe


def plot_pca_tags(data: pd.DataFrame, n_components_for_variance: int = 0, show_plots: str = "") -> tuple[list[str], PCA, int]:
    """
    Perform Principal Component Analysis (PCA) on tag data and plot the results.

    Parameters:
    -----------
    data (pd.DataFrame): DataFrame containing tag data.

    n_components_for_variance (int, optional): Number of components to retain for variance analysis. Default is 0.

    show_plots (str, optional): Flag to specify which plots to display. Default is an empty string.

    Return:
    -----------
    tuple: A tuple containing a list of group names, PCA model, and the number of components for 80% variance.
    """
    column_names = list(data.columns)
    # You need to define assign_group function
    groups = assign_group(data, column_names)
    group_list = set(groups)
    # You need to define transform_dataframe function
    X, y, targets, features = transform_dataframe(data, groups, group_list)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    pca = PCA()
    pca.fit_transform(X_scaled)

    explained_variance_ratio = pca.explained_variance_ratio_
    cumulative_variance = np.cumsum(explained_variance_ratio)
    num_components_for_80_variance = np.argmax(cumulative_variance >= 0.80) + 1
    num_components_for_95_variance = np.argmax(cumulative_variance >= 0.95) + 1

    def plots(show_plots):
        """
        Display multiple plots based on user-selected options.

        Parameters:
        -----------
        show_plots (str): String containing user-selected plots separated by "and".

        Return:
        -----------
        None
        """
        plot_mapping = {
            "scree": {"function": plot_scree},
            "saturation": {"function": plot_saturation},
            "loading": {"function": plot_loading}
        }

        selected_plots = show_plots.split("and")
        num_subplots = len(selected_plots)

        fig, axes = plt.subplots(1, num_subplots, figsize=(18, 5))
        if num_subplots == 1:
            axes = [axes]

        for i, selected_plot in enumerate(selected_plots):
            plot_info = plot_mapping[selected_plot.strip()]
            plot_function = plot_info["function"]

            if num_subplots == 1:
                subplot_index = 0
            else:
                subplot_index = i

            ax = axes[subplot_index]
            plot_function(ax)

        plt.tight_layout()
        plt.show()

    def plot_scree(ax):
        """
        Plot the scree plot showing explained variance ratios for each principal component.

        Parameters:
        -----------
        ax: Matplotlib axis.
        """
        ax.plot(range(1, len(explained_variance_ratio) + 1),
                explained_variance_ratio, marker='o')
        ax.set_xlabel('Principal Component')
        ax.set_ylabel('Explained Variance Ratio')
        ax.set_title('Scree Plot')

    def plot_saturation(ax):
        """
        Plot the saturation plot showing cumulative explained variance.

        Parameters:
        -----------
        ax: Matplotlib axis.
        """
        ax.plot(range(1, len(cumulative_variance) + 1),
                cumulative_variance, marker='o', color='r')
        ax.set_xlabel('Number of Principal Components')
        ax.set_ylabel('Cumulative Explained Variance')
        ax.set_title('Saturation Plot')
        ax.text(y=0.05, x=0.5,
                s=f"Number of components needed for 95% explained variance: {num_components_for_95_variance}")

    def plot_loading(ax):
        """
        Plot the loading plot showing the contribution of features to principal components.

        Parameters:
        -----------
        ax: Matplotlib axis.
        """
        loading_matrix = pca.components_.T * np.sqrt(pca.explained_variance_)
        for i, feature in enumerate(features):
            ax.arrow(0, 0, loading_matrix[i, 0],
                     loading_matrix[i, 1], color='r', alpha=0.5)
            ax.text(loading_matrix[i, 0],
                    loading_matrix[i, 1], feature, color='g')
        ax.set_xlim(-1, 1)
        ax.set_ylim(-1, 1)
        ax.set_xlabel('PC1 Loading')
        ax.set_ylabel('PC2 Loading')
        ax.set_title('Loading Plot')

    def find_contributing_tags(components: int):
        """
        Find the main contributing tags explaining a specified percentage variance.

        Parameters:
        -----------
        components (int): Percentage of explained variance.

        Return:
        -----------
        list: List of main contributing tags.
        """
        loading_matrix = pca.components_.T * np.sqrt(pca.explained_variance_)
        main_tags = []
        for loading in loading_matrix:
            indices_sorted_by_loading = np.argsort(np.abs(loading))[::-1]
            main_tags.append(features[indices_sorted_by_loading[0]])
        main_tags = sorted(set(main_tags[:components]))
        main_tags_str = ', '.join(main_tags)
        print(
            f'Main contributing tags for {components}% explained variance: {main_tags_str}')
        return main_tags

    if "all" in show_plots:
        show_plots = "loading and scree and saturation"

    if n_components_for_variance > 0:
        main_tags = find_contributing_tags(n_components_for_variance)
    else:
        main_tags = []

    if show_plots:
        plots(show_plots)
        plt.show()

    return main_tags, pca, num_components_for_80_variance


def retrieve_pca_components(input_file: str, output: str, variables: str, id: str, tag_length: int,  number_of_records: int, n_components_for_variance: int, show_plots: str):
    """
    Retrieve principal components after merging similar tags, dropping unique columns, and performing PCA.

    Parameters:
    -----------
    input_file (str): Path to the input file.

    output (str): Path to the output file.

    variables (str): Variable names in the input file.

    id (str): Identifier column name.

    tag_length (int): Length of tags to consider for merging.

    number_of_records (int): Number of records to consider.

    n_components_for_variance (int): Number of components to retain for variance analysis.

    show_plots (str): String containing user-selected plots separated by "and".

    Return:
    -----------
    list: List of principal components.
    """
    Dataframe_merged = merge_similar_tags_from_dataframe(
        input_file, output, variables, id, tag_length, number_of_records)
    Dataframe_filtered = drop_unique_columns(Dataframe_merged)
    components = plot_pca_tags(
        Dataframe_filtered, n_components_for_variance, show_plots)[0]
    return components


def retrieve_clusters(input_file: str, output: str, variables: str, id: str, tag_length: int, number_of_records: int, n_components_for_variance: int, show_plots: str, transpose: bool = False, label: bool = False, max_clusters: int = 20, visualize_clusters: bool = False, save_clusters: str = "separate") -> pd.DataFrame:
    """
    Retrieve clusters of source documents by tag similarity via performing PCA and k-means clustering.

    Parameters:
    -----------
    input_file (str): Path to the input file.

    output (str): Path to the output file.

    variables (str): Variable names in the input file.

    id (str): Identifier column name.

    tag_length (int): Length of tags to consider for merging.

    number_of_records (int): Number of records to consider.

    n_components_for_variance (int): Number of components to retain for variance analysis.

    show_plots (str): String containing user-selected plots separated by "and".

    transpose (bool, optional): Flag to transpose the DataFrame. Default is False.

    label (bool, optional): Flag to label the clusters. Default is False.

    max_clusters (int, optional): Maximum number of clusters for k-means. Default is 20.

    visualize_clusters (bool, optional): Flag to visualize clusters in 3D. Default is False.

    save_clusters (str): Determines how to save the clusters: separate/merged/all. Any other value does not generate csv output, but only returns the dataframe.

    Return:
    -----------
    pd.DataFrame: DataFrame containing clustered data.
    """
    def retrieve_metadata_from_title(dataframe_a: pd.DataFrame, title_col_a: str, dataframe_b: pd.DataFrame, title_col_b: str) -> pd.DataFrame:
        "Return all data for each row in dataframe_b that has a matching title in dataframe_a"
        merged_df = pd.merge(dataframe_a[title_col_a], dataframe_b,
                             how='inner', left_on=title_col_a, right_on=title_col_b)
        return merged_df

    def perform_kmeans_clustering(scores_pca, max_clusters):
        "Perform k-means clustering up to the max cluster size and return a figure showing the inertia/fit."
        wcss = []
        for i in range(1, (max_clusters + 1)):
            kmeans_pca = KMeans(
                n_clusters=i, init='k-means++', random_state=42)
            kmeans_pca.fit(scores_pca)
            wcss.append(kmeans_pca.inertia_)

        plt.clf()
        plt.plot(wcss)
        plt.show()

        number_of_clusters = int(input("Select the number of clusters: "))
        kmeans_pca = KMeans(n_clusters=number_of_clusters, init='k-means++', random_state=42)
        kmeans_pca.fit(scores_pca)
        return number_of_clusters, kmeans_pca

    def visualize_clusters_3d(Dataframe_filtered_kmeans):
        """
        Visualize clusters in 3D space based on PCA components.

        Parameters:
        -----------
        Dataframe_filtered_kmeans (pd.DataFrame): DataFrame containing PCA components and cluster labels.
        """
        unique_clusters = Dataframe_filtered_kmeans['Cluster'].unique()
        colors = cm.viridis(np.linspace(0, 1, len(unique_clusters)))

        grid_dimension = ceil(sqrt(len(unique_clusters)))
        fig, axs = plt.subplots(grid_dimension, grid_dimension, figsize=(15, 15), subplot_kw={'projection': '3d'})
        axs = axs.flatten()

        for ax, cluster_label, color in zip(axs, unique_clusters, cycle(colors)):
            cluster_data = Dataframe_filtered_kmeans[Dataframe_filtered_kmeans['Cluster'] == cluster_label]
            ax.scatter(cluster_data['component 1'], cluster_data['component 2'], cluster_data['component 3'], 
                    c=[color], label=f'Cluster {cluster_label}')
            ax.set_title(f'Cluster {cluster_label}')

        for i in range(len(unique_clusters), len(axs)):
            axs[i].axis('off')
        plt.show()

    def save_cluster_data(Dataframe_filtered_kmeans, input_file, file_name, number_of_clusters, separator: str = ";") -> None:
        """
        Save cluster data to separate CSV files for each cluster.

        Parameters:
        -----------
        Dataframe_filtered_kmeans (pd.DataFrame): DataFrame containing PCA components and cluster labels.

        input_file (str): Path to the input file.

        file_name (str): Base name for the output CSV files.

        number_of_clusters (int): Number of clusters.
        """
        for i in range(number_of_clusters):
            cluster = i + 1
            cluster_data_PCA = Dataframe_filtered_kmeans[
                Dataframe_filtered_kmeans['Cluster'] == i].copy()
            cluster_data_PCA.rename(columns={list(cluster_data_PCA)[
                                    0]: 'Article Title'}, inplace=True)
            input_csv = pd.read_csv(input_file, sep = separator)
            cluster_original_data = retrieve_metadata_from_title(
                cluster_data_PCA, "Article Title", input_csv, "Article Title")
            cluster_file_name = f"C:/NLPvenv/NLP/output/csv/{file_name}_cluster_{cluster}.csv"
            cluster_original_data.to_csv(cluster_file_name, index=False)
    
    def save_cluster_data_merged(Dataframe_filtered_kmeans, input_file, file_name, number_of_clusters, separator: str = ";") -> None:
        """
        Save cluster data to separate CSV files for each cluster.

        Parameters:
        -----------
        Dataframe_filtered_kmeans (pd.DataFrame): DataFrame containing PCA components and cluster labels.

        input_file (str): Path to the input file.

        file_name (str): Base name for the output CSV files.

        number_of_clusters (int): Number of clusters.
        """
        clusters_original_data = pd.DataFrame()
        for i in range(number_of_clusters):
            cluster = i + 1
            cluster_data_PCA = Dataframe_filtered_kmeans[
                Dataframe_filtered_kmeans['Cluster'] == i].copy()
            cluster_data_PCA.rename(columns={list(cluster_data_PCA)[
                                    0]: 'Article Title'}, inplace=True)
            input_csv = pd.read_csv(input_file, sep = separator)
            cluster_original_data = retrieve_metadata_from_title(
                cluster_data_PCA, "Article Title", input_csv, "Article Title")
            cluster_original_data["Cluster"] = cluster
            clusters_original_data = pd.concat([clusters_original_data, pd.DataFrame(cluster_original_data)])
            clusters_original_data = clusters_original_data.reset_index(drop=True)
        
        cluster_file_name = f"C:/NLPvenv/NLP/output/csv/{file_name}_all_clusters.csv"
        clusters_original_data.to_csv(cluster_file_name, index=False)
            

    Dataframe_merged = merge_similar_tags_from_dataframe(
        input_file, output, variables, id, tag_length, number_of_records)
    Dataframe_filtered = drop_unique_columns(Dataframe_merged)

    if transpose:
        Dataframe_filtered = Dataframe_filtered.transpose()

    x, pca, num_components_for_80_variance = plot_pca_tags(
        Dataframe_filtered, n_components_for_variance, show_plots)

    pca = PCA(num_components_for_80_variance)
    pca.fit(Dataframe_filtered)
    scores_pca = pca.transform(Dataframe_filtered)

    number_of_clusters, kmeans_pca = perform_kmeans_clustering(scores_pca, max_clusters)


    Dataframe_filtered['Cluster'] = kmeans_pca.labels_

    Dataframe_filtered_kmeans = pd.concat(
        [Dataframe_filtered.reset_index(), pd.DataFrame(scores_pca)], axis=1)
    Dataframe_filtered_kmeans.columns.values[-3:] = [
        'component 1', 'component 2', 'component 3']
    Dataframe_filtered_kmeans['Cluster'] = kmeans_pca.labels_

    if visualize_clusters:
        visualize_clusters_3d(Dataframe_filtered_kmeans)

    file_path = os.path.basename(input_file)
    file_name = os.path.splitext(file_path)[0]

    if save_clusters == "merged" or save_clusters == "all":
        save_cluster_data_merged(Dataframe_filtered_kmeans,
                          input_file, file_name, number_of_clusters)

    if save_clusters == "separate" or save_clusters == "all":
        save_cluster_data(Dataframe_filtered_kmeans,
                          input_file, file_name, number_of_clusters)
    return Dataframe_filtered_kmeans

if __name__ == "__main__":
    dataframe = retrieve_clusters(input_file="C:/NLPvenv/NLP/output/csv/savedrecs_lianas_sorted.csv", output="",
                                  variables="Keywords", id="Article Title", tag_length=4,  number_of_records="", n_components_for_variance=0, show_plots="", transpose=False, max_clusters=20, save_clusters = "all", visualize_clusters=True)
