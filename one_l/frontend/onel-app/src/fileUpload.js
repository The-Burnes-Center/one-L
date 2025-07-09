/*
TODO: access keys to be moved to .env file
stack is currently hardcoded and is storagestack. Needs to be changed to main stack
Currently taking only one file. Needs to be changed to multiple files
*/

import React, { useState, useEffect } from 'react';
import AWS from 'aws-sdk';
    

const FileUpload = () => {
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadStatus, setUploadStatus] = useState('');
    const [bucketName, setBucketName] = useState('');

  // Configure AWS SDK
  AWS.config.update({
    accessKeyId: 'AKIA3YCD2VRXLL37AEVK', // Replace with your access key
    secretAccessKey: 'F8gVVYv3Sv0DGZe3c/4i+c0HcaBDyiE+oi1ngM5W', // Replace with your secret key
    region: 'us-east-1' // Replace with your bucket's region
  });

  const s3 = new AWS.S3();

  const cloudformation = new AWS.CloudFormation();

  useEffect(() => {
    // Fetch the bucket name from CloudFormation outputs
    cloudformation.describeStacks({ StackName: 'OneLStackStorageStack0262469F' }, (err, data) => {
      if (err) {
        console.error('Error fetching stack outputs:', err);
      } else {
        console.log('Stack Data:', data);
        const outputs = data.Stacks[0].Outputs;
        console.log('Outputs:', outputs);
        const bucketOutput = outputs.find(output => output.OutputKey === 'CustomFilesUploadBucketName');
        if (bucketOutput) {
          setBucketName(bucketOutput.OutputValue);
        }
      }
    });
  }, []);

  const handleFileChange = (event) => {
    setSelectedFile(event.target.files[0]);
  };

  const handleUpload = () => {
    console.log('Upload button clicked');
    if (!selectedFile) {
      console.error('No file selected');
      setUploadStatus('Please select a file to upload.');
      return;
    }
    if (!bucketName) {
      console.error('Bucket name not set');
      setUploadStatus('Bucket name is not set. Please try again later.');
      return;
    }

    console.log('Selected File:', selectedFile);
    console.log('Bucket Name:', bucketName);

    const params = {
      Bucket: bucketName,
      Key: selectedFile.name,
      Body: selectedFile
    };

    s3.upload(params, (err, data) => {
      if (err) {
        console.error('Error uploading file:', err);
        setUploadStatus('Upload failed. Please try again.');
      } else {
        console.log('File uploaded successfully:', data.Location);
        setUploadStatus(`Upload successful! File URL: ${data.Location}`);
      }
    });
  };

  return (
    <div>
      <input type="file" onChange={handleFileChange} />
      <button onClick={handleUpload}>Upload</button>
      {uploadStatus && <p>{uploadStatus}</p>}
    </div>
  );
};

export default FileUpload;